package practicas;

import java.util.Arrays;
import java.util.Random;

public class arrayComprobacionOrdenamiento {
    public static void main(String[] args) {
        int[] NumerosAleatorios = new int[20];

        for (int i = 0; i < 20; i++) {
            Random r = new Random();
            NumerosAleatorios[i] = r.nextInt(10);
        }
        System.out.println("Los numeros son: " + Arrays.toString(NumerosAleatorios));
        System.out.println(OrdenarArray(NumerosAleatorios) ? "Esta ordenado" : "No esta ordenado");
    }
    public static boolean OrdenarArray(int[] array) {
        if (array == null || array.length <= 1) {
            return true;
        }
        for (int i = 0; i < array.length - 1; i++) {
            if (array[i] < array[i+1]) {
                return false;
            }
        }
        return true;
    }
}

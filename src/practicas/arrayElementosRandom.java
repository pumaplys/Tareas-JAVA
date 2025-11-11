package practicas;

import java.util.Arrays;
import java.util.Random;

public class arrayElementosRandom {
    public static void main(String[] args) {

        int[] NumerosAleatorios = new int[20];

        for (int i = 0; i < 20; i++) {
            Random r = new Random();
            NumerosAleatorios[i] = r.nextInt(10);
        }
        System.out.println("Los numeros son: " + Arrays.toString(NumerosAleatorios));
    }
}

package tema2.t03;

import java.util.Scanner;

public class ej05 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Digite un numero: ");
        int numero = sc.nextInt();
        sc.close();

        String par = " Es par";
        String impar = " Es impar";
        String comprobacion = ParOImpar(numero, par, impar);

        System.out.println(numero + comprobacion);
    }

    static String ParOImpar(int numero, String par, String impar) {
        if (numero % 2 == 0) {
            return par;
        } else {
            return impar;
        }

    }
}

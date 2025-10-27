package tema2.t03;

import java.util.Scanner;

public class ej07 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Cuantos grados celsius quieres convertir");
        int c = sc.nextInt();
        sc.close();

        double resul = Conversion(c);

        System.out.println(c + "C a falhereit son: " + resul);
    }

    static double Conversion(int c) {
        return (1.8 * c) + 32;
    }
}
